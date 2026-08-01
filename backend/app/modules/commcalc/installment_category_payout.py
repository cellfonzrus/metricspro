"""FLAT (ONE-TIME) PAYOUT BY DEVICE CATEGORY — migration 256.

OWNER DIRECTIVE 2026-08-01 (verbatim): "fwa is paid on flat rate should not be in monthly payments -
fix but dont hard code".

WHAT THIS IS
------------
A per-tenant switch, PER DEVICE CATEGORY, that takes a qualifying activation OUT of the M1..M6
monthly-installment chain and pays it ONCE, as a flat dollar amount the owner types. It is the sibling
of `installment_category.py` (mig 245, "does this category qualify at all") and reuses that module's
category vocabulary and its three-layer config ladder verbatim:

    1. per SCHEDULE   — plan_installment_schedule.category_payout      (jsonb; NULL = inherit)
    2. per ORG        — commission_org_config.installment_category_payout (jsonb; NULL = defaults)
    3. code defaults  — DEFAULT_PAYOUT below = every category on 'installments', i.e. TODAY'S BEHAVIOUR.

NOTHING IS HARD-CODED. There is no 'luxelink', no 'FWA', no 'home_internet' literal in any decision
here or in the engine. A category is identified by the tenant's own `installment_category` rules (its
own rows first, the built-in tail second); this module only answers "how does THIS tenant pay THAT
category". A different tenant can put `phone` on flat and leave `home_internet` on installments.

THE DOLLAR IS THE OWNER'S — NEVER OURS
--------------------------------------
`amount` has NO default and NO seed. The owner directive stated the MECHANISM (flat, not monthly), not
the NUMBER. So:

  * mode='installments' (the default)         -> today's behaviour, silent. This is every tenant today.
  * mode='flat_once' AND amount is a number   -> the chain pays that amount ONCE and no other month.
  * mode='flat_once' AND amount is NULL       -> NOT ACTIVE. The chain keeps paying exactly as it does
                                                today, and the engine raises a LOUD
                                                `flat_amount_unconfigured` warning naming the category.
                                                We never guess a payout and we never manufacture a $0.

That last branch is the whole safety argument: a half-configured switch cannot zero anybody. It is the
same discipline `installment_category` uses for `unknown` (pay and shout, never silently drop).

WHICH MONTH THE FLAT LANDS IN
-----------------------------
`pay_month` (default 1 = the sale month, which is what "not in monthly payments" means). It is config
because a tenant may want the one-time payment to land after the first residual posts. It is CLAMPED
into 1..num_months for the schedule, so a mis-typed pay_month can never make the chain pay NOTHING —
it lands on the last month of the schedule and the clamp is reported.

THE PAID GATE IS UNCHANGED — DELIBERATELY. Flat mode changes (a) the AMOUNT of the paying month and
(b) the EXISTENCE of the other months. It does not touch `gate_mode` / `m1_gate` / `gate_from_month`.
Whether a one-time payment should still wait on "we pay as we get paid" is a separate owner decision,
so this package does not silently make it.

PURE: every function takes its config as an argument. The only I/O is `load_org_payout`, which
degrades to the code defaults when migration 256 is unapplied (so this file is a no-op until the owner
configures something, with or without the SQL).
"""
from app.modules.commcalc import installment_category as icat

# The categories are NOT redefined here — they are the mig-245 vocabulary, so the two switches on the
# Plan Installments page can never drift apart.
CATEGORY_KEYS = icat.CATEGORY_KEYS
CATEGORY_LABELS = icat.CATEGORY_LABELS

# 'installments' = the M1..M6 chain exactly as it works today. 'flat_once' = one payment, then nothing.
PAYOUT_MODES = ("installments", "flat_once")
MODE_LABELS = {
    "installments": "Monthly installments (M1…MN, as configured on the schedule)",
    "flat_once": "One-time FLAT amount (leaves the monthly chain entirely)",
}

# EVERY category on 'installments', every amount NULL. This IS today's behaviour, so a tenant that
# configures nothing — i.e. all of them, right now — is byte-identical to the pre-256 engine.
DEFAULT_PAYOUT = {k: {"mode": "installments", "amount": None, "pay_month": 1} for k in CATEGORY_KEYS}

MAX_PAY_MONTH = 12


def _num(v):
    """A float, or None. Blank string / None / garbage -> None (NOT 0.0 — a blank amount must stay
    UNCONFIGURED, because 0.0 is a decision and blank is the absence of one). PURE."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s.replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def normalize_payout(stored):
    """A stored payout config (dict of category -> dict|number|None) -> the full per-category dict, with
    the code defaults filling every category the tenant did not state. PURE; never raises.

    Accepts three shapes so a hand-written jsonb still works:
      {'home_internet': {'mode':'flat_once','amount':25,'pay_month':1}}   the canonical shape
      {'home_internet': 25}                                              a bare number = flat at that amount
      {'home_internet': None}                                            explicit "not configured"
    """
    out = {k: dict(v) for k, v in DEFAULT_PAYOUT.items()}
    if not isinstance(stored, dict):
        return out
    for k in CATEGORY_KEYS:
        if k not in stored:
            continue
        raw = stored[k]
        if raw is None:
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[k] = {"mode": "flat_once", "amount": float(raw), "pay_month": 1}
            continue
        if not isinstance(raw, dict):
            continue
        mode = str(raw.get("mode") or "installments").strip().lower()
        if mode not in PAYOUT_MODES:
            mode = "installments"
        amt = _num(raw.get("amount"))
        pm = _int(raw.get("pay_month"), 1)
        pm = 1 if pm < 1 else (MAX_PAY_MONTH if pm > MAX_PAY_MONTH else pm)
        out[k] = {"mode": mode, "amount": amt, "pay_month": pm}
    return out


def payout_for(sched, org_payout):
    """(payout dict, source) for ONE schedule: its own `category_payout` when it states any, else the
    org's, else the code defaults. Mirrors icat.qualification_for exactly. PURE."""
    own = (sched or {}).get("category_payout")
    if isinstance(own, dict) and own:
        return normalize_payout(own), "schedule"
    if isinstance(org_payout, dict) and org_payout.get("_stored"):
        return normalize_payout({k: v for k, v in org_payout.items() if k in CATEGORY_KEYS}), "org"
    return {k: dict(v) for k, v in DEFAULT_PAYOUT.items()}, "default"


def resolve_flat(category, payout_cfg, num_months=1):
    """How ONE chain of `category` is paid, under `payout_cfg` (already normalized). PURE.

    Returns a dict, always with the same keys:
      mode          'installments' | 'flat_once'  (what the tenant ASKED for)
      active        True only when a flat amount is actually resolvable — the ONLY flag the engine acts on
      amount        the flat dollars (None when not active)
      pay_month     the month_index the single payment lands on, CLAMPED into 1..num_months
      pay_month_requested / clamped
      reason        None | 'amount_unconfigured'   (why an asked-for flat is not active)

    `active=False` ALWAYS means "behave exactly as today". There is no branch in which this module
    reduces a payout without an owner-entered number.
    """
    cfg = (payout_cfg or {}).get(category) or {}
    mode = str(cfg.get("mode") or "installments").strip().lower()
    if mode not in PAYOUT_MODES:
        mode = "installments"
    amt = _num(cfg.get("amount"))
    n = max(1, min(MAX_PAY_MONTH, _int(num_months, 1)))
    want = max(1, min(MAX_PAY_MONTH, _int(cfg.get("pay_month"), 1)))
    landed = min(want, n)
    out = {"mode": mode, "active": False, "amount": None, "pay_month": landed,
           "pay_month_requested": want, "clamped": landed != want, "reason": None}
    if mode != "flat_once":
        return out
    if amt is None:
        # THE NO-GUESS BRANCH. The owner switched the category to flat but has not typed the dollar.
        # We do NOT pay 0, we do NOT invent a rate — the chain keeps paying exactly as it does today
        # and the engine shouts. See the module header.
        out["reason"] = "amount_unconfigured"
        return out
    out["active"] = True
    out["amount"] = round(float(amt), 2)
    return out


def configured_categories(payout_cfg):
    """The categories the tenant put on flat, for reporting. PURE."""
    return sorted(k for k in CATEGORY_KEYS
                  if str(((payout_cfg or {}).get(k) or {}).get("mode") or "") == "flat_once")


def describe(category, flat):
    """One human sentence for a warning / a UI badge. PURE."""
    label = CATEGORY_LABELS.get(category, category)
    if flat.get("active"):
        return (f"'{label}' is paid as a ONE-TIME flat ${flat['amount']:,.2f} in month "
                f"{flat['pay_month']}; the remaining installment months do not pay.")
    if flat.get("reason") == "amount_unconfigured":
        return (f"'{label}' is set to a one-time FLAT payout but no amount has been entered, so it is "
                f"still paying monthly installments exactly as before. Enter the amount under Plan "
                f"Installments -> Flat payout by category.")
    return f"'{label}' pays monthly installments."


# ── loader (the only I/O; degrades to the code default with migration 256 unapplied) ────────────────
def load_org_payout(client, org_id):
    """The org-level payout config. Carries `_stored` so payout_for can tell "the tenant saved this"
    from "nothing configured". Missing column/table -> defaults. Never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("installment_category_payout")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("installment_category_payout")
    except Exception:
        stored = None
    out = normalize_payout(stored)
    out["_stored"] = isinstance(stored, dict) and bool(stored)
    return out
