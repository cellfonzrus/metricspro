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


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# THE PAY-FEED BALANCE — every dollar is placed, or it is REPORTED as unplaced with a reason.
#
# OWNER 2026-10-06: *"the numbers are off"*. They were, by $340,000 in August alone, and nothing on
# any screen said so. The three defects behind it were different, but their SHAPE was identical:
#
#     **the pay path treats "I could not place this money" as "there is no money".**
#
# Measured live, house org (Boost), 2026-10-06:
#
#   · the carrier paid $408,989.99 of August payment detail; the engine placed $68,479.60
#   · $288,813.11 of it (71%) carried a `payment_type` absent from `payment_categories` — six
#     QUARTER-NAMED promo lines ("2026 Q3 Promo PIC Offer", …). March and April were 100% mapped;
#     the gap opened in May at $4,679 and grows every quarter as the carrier renames its promos
#   · $16,952.28 across 73 rep logins was categorised correctly but named a rep who rang no sale
#     that month, so `pay_by_login` held it and no rep row ever read it (September: $16,217.08/78)
#   · July's read was truncated at a literal 50,000 rows against 82,999 (see `core/feed_read.py`)
#
# Each one was silent because no total had to balance. So this is the total that has to balance:
# `reconcile_pay_feed` accounts for every row's amount as either PLACED in a pay bucket or UNPLACED
# under a NAMED reason, and `balances` is the arithmetic proof that nothing fell between them.
#
# ABSENCE IS NEVER A FINDING, and nor is this module a classifier: it does not decide what a
# category means, which logins are real, or whether a promo SHOULD pay. It is handed the org's own
# category map and the set of logins the engine resolved, and it reports what the engine could not
# place. The decision about an unmapped promo is a money decision and the owner's call (§6d
# precedent) — this makes the decision VISIBLE instead of making it silently as $0.
#
# PURE. No DB, no network, no imports beyond the stdlib. Never imported by the calculate path.
# ──────────────────────────────────────────────────────────────────────────────────────────────────

# The buckets `calculator.calc_rep_commissions` actually reads off `pay_by_login`. A category
# outside this set is mapped but unhandled — real money the engine has nowhere to put. Note
# `Chargeback`: the calculator tests for it and `payment_categories` has never contained it, so
# that bucket has always been $0 and the reconciliation now says so out loud.
PLACEABLE_CATEGORIES = ("Commission", "Re-imbursement", "MDF", "Chargeback")

# Why a dollar did not reach a rep. The reason is part of the report, never a silent default.
UNPLACED_REASONS = {
    "unmapped_payment_type":
        "The carrier used a payment type this org has never mapped to a pay category, so it "
        "belongs to no bucket. Quarter-named promos land here every time the carrier renames them.",
    "unhandled_category":
        "The payment type maps to a category the pay engine does not read, so the money is "
        "categorised but still goes nowhere.",
    "unresolved_rep":
        "The category is right, but the carrier named a rep login that rang no sale in this "
        "period, so no rep row ever reads it.",
    "no_rep_named":
        "The carrier row carries no rep login at all, so there is nobody to attribute it to.",
}

UNCATEGORISED = "Unknown"


def _money(v):
    return round(_f(v), 2)


def reconcile_pay_feed(rows, category_of, placed_logins,
                       amount_key="amount", type_key="payment_type",
                       login_key="rep_username"):
    """Account for every dollar in a period's payment-detail feed.

    `category_of` maps a raw `payment_type` string to this org's pay category (or `"Unknown"` /
    `None` when the org has never mapped it) — the caller's own `payment_categories` map, never a
    second copy of it here. `placed_logins` is the set of lower-cased rep logins the engine
    actually resolved for the period; a login outside it reaches no rep row.

    Returns the feed total, what was placed, and what was not, broken out by reason, plus
    `balances` — the arithmetic proof that `placed + unplaced == feed_total` to the cent. A caller
    presenting a pay figure while `balances` is true and `unplaced` is non-zero is presenting a
    number that is short by a KNOWN amount, and can say so.
    """
    placed_set = {str(x or "").lower().strip() for x in (placed_logins or ())}
    feed_total = 0.0
    placed = 0.0
    by_reason = {k: {"amount": 0.0, "rows": 0} for k in UNPLACED_REASONS}
    by_category = {}
    unmapped_types = {}
    unresolved_logins = {}

    for r in (rows or []):
        amt = _f(r.get(amount_key))
        feed_total += amt
        ptype = str(r.get(type_key) or "").strip()
        login = str(r.get(login_key) or "").lower().strip()
        cat = category_of(ptype) if callable(category_of) else (category_of or {}).get(ptype)
        cat = str(cat or UNCATEGORISED).strip() or UNCATEGORISED

        def _unplaced(reason):
            by_reason[reason]["amount"] += amt
            by_reason[reason]["rows"] += 1

        if cat == UNCATEGORISED:
            _unplaced("unmapped_payment_type")
            d = unmapped_types.setdefault(ptype, {"payment_type": ptype, "amount": 0.0, "rows": 0})
            d["amount"] += amt
            d["rows"] += 1
            continue
        if cat not in PLACEABLE_CATEGORIES:
            _unplaced("unhandled_category")
            continue
        if not login:
            _unplaced("no_rep_named")
            continue
        if login not in placed_set:
            _unplaced("unresolved_rep")
            d = unresolved_logins.setdefault(login, {"login": login, "amount": 0.0, "rows": 0})
            d["amount"] += amt
            d["rows"] += 1
            continue
        placed += amt
        c = by_category.setdefault(cat, {"category": cat, "amount": 0.0, "rows": 0})
        c["amount"] += amt
        c["rows"] += 1

    unplaced = sum(v["amount"] for v in by_reason.values())
    for v in by_reason.values():
        v["amount"] = _money(v["amount"])
    for d in (by_category, unmapped_types, unresolved_logins):
        for v in d.values():
            v["amount"] = _money(v["amount"])

    feed_total, placed, unplaced = _money(feed_total), _money(placed), _money(unplaced)
    return {
        "feed_total": feed_total,
        "placed": placed,
        "unplaced": unplaced,
        # The proof. Cent-exact: the three figures are each rounded from the same pass, so a
        # mismatch means a row escaped the branch set above, not float drift.
        "balances": abs((placed + unplaced) - feed_total) < 0.011,
        "placed_pct": round(100.0 * placed / feed_total, 2) if feed_total else 0.0,
        "by_reason": {k: {**v, "label": UNPLACED_REASONS[k]} for k, v in by_reason.items()
                      if v["rows"]},
        "placed_by_category": sorted(by_category.values(), key=lambda x: -abs(x["amount"])),
        "unmapped_payment_types": sorted(unmapped_types.values(), key=lambda x: -abs(x["amount"])),
        "unresolved_rep_logins": sorted(unresolved_logins.values(), key=lambda x: -abs(x["amount"])),
    }


def day_coverage_gap(detail_days, statement_days):
    """Which days one feed has and the other does not, for two feeds that represent the SAME money.

    `raw_payment_detail` and `raw_comp_report` are the per-line and the statement view of one
    carrier's payments, and October 2026 proves they tie: two days present in both,
    $15,460.69 = $15,460.69 to the penny. So a day in one and not the other is a feed that arrived
    incomplete, not a disagreement to reconcile away.

    Measured live 2026-10-06: the statement is missing the FINAL DAY OF EVERY MONTH — 03-31, 04-30,
    05-31, 06-30, 07-31, 08-31, 09-30 — $10,875.67 to $23,050.60 a month, an end-date-exclusive
    off-by-one in whatever window produced each pull. August is missing 08-21…08-30 from the detail
    side as well, a pull that never ran.

    Returns the two one-sided day lists and `complete` — true only when both feeds cover the same
    days. A month whose coverage is incomplete must not be presented as a finished month.
    """
    d = {str(x) for x in (detail_days or ()) if str(x or "").strip()}
    s = {str(x) for x in (statement_days or ()) if str(x or "").strip()}
    only_detail = sorted(d - s)
    only_statement = sorted(s - d)
    return {
        "days_detail": len(d),
        "days_statement": len(s),
        "missing_from_statement": only_detail,
        "missing_from_detail": only_statement,
        "complete": not only_detail and not only_statement,
    }
