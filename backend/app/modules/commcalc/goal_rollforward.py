"""THE ONE HOME for "what is next month's goal, given this month's goal and what was achieved".

Index §COMMCALC-84. Proof: `backend/harness_goal_rollforward.py`. Migration `1131`.

OWNER DIRECTIVE 2026-11-25 (Sanjot, verbatim): "Update the Nov goals for boost same as Oct and set
that as default for tenant as we discussed - it will be the same if they did not achieve the goal if
they met the goal then it will be 10% higher than they achieved".

THE RULE, in one line: a MISSED goal carries forward unchanged; a MET goal becomes `factor` times
what the store ACTUALLY ACHIEVED; a category with no prior goal has nothing to roll and says so.

WHY THIS MODULE EXISTS — two defects, one class
───────────────────────────────────────────────
`router._carry_forward_map` already carried a missed goal forward and stretched a met one. It was
wrong in two ways that are the same way:

 1. It stretched to 10% above the GOAL, not above the ACHIEVEMENT. On live October 2026 house data
    every one of the five stores that beat its goal got the smaller stretch (B-117 upgrades: 52
    achieved, old reading 50, declared reading 57). The old rule measured the PLAN; the owner asked
    for the PERFORMANCE to be measured. Worst where a store does best, which is the wrong way round.

 2. The 10% was `STRETCH_FACTOR = 1.10`, a module constant in `router.py`. RULE TWO: behaviour is
    per-org config rows with house defaults. A tenant wanting 5%, or wanting to stretch off the
    plan, had nowhere to say so — and a constant cannot be a per-tenant answer.

Both are the same class: **how one month's goal follows from the last is a POLICY, and a policy is
DECLARED with a house default, never compiled in.** So the arithmetic and the declaration live here,
once, and every caller dereferences them. `harness_goal_rollforward.py` §H fails the build if the
router grows a second copy of either (scanned over tokenised code — a docstring mentioning the name
is documentation, not a dependency).

A HOUSE DEFAULT IS CORRECT HERE, AND THAT IS NOT A CONTRADICTION. The 2026-10-30 directive bans a
default that is really one tenant's VOCABULARY — a tenant's own words standing behind a parameter
nobody passed. This is a POLICY: a rate, a basis and a switch, which the same directive names as
exactly where a house default remains right. What is NOT defaulted is the tenant: `policy_for` takes
`org_id` as a required argument, so a caller that has not asked which tenant cannot get an answer.

WHAT 'new' MEANS, AND WHY IT IS NOT ZERO. A category with no prior goal returns `value=None` with
basis `'new'`. A zero would be a goal of zero — a plausible-looking number no human set, which is
how this class of defect hides (the §COMMCALC-49.10 lesson: zero reads as an answer). The surface
renders `None` as blank, meaning "no goal", and the writer never persists it.

NOT MONEY. `targets_engine.achieved_for_cat` pays on ACTUALS and never reads a goal, so nothing this
module returns can move a payout. It sets what a store is ASKED to do, not what anyone is paid.
"""
from __future__ import annotations

# ── THE HOUSE DEFAULT — declared once, here ───────────────────────────────────────────────────────
# `stretch_basis`:  'achieved' = factor x what the store achieved (the owner's rule, 2026-11-25)
#                   'target'   = factor x the goal it was set (the pre-2026-11-25 reading, kept so a
#                                tenant that prefers to stretch the PLAN can declare it rather than
#                                needing code)
# `stretch_factor`: the multiplier applied when the goal was met. Never below 1.0 — a "stretch" that
#                   lowers a goal a store just beat is not a reading of this rule, it is a typo, and
#                   a typo in a config row must not quietly cut every goal on the platform.
# `auto_fill`:      whether next month's goals are written without anyone pressing anything.
#                   ON by default for EVERY tenant (owner's choice, decision card 2026-11-25).
HOUSE_DEFAULT: dict = {
    "stretch_basis": "achieved",
    "stretch_factor": 1.10,
    "auto_fill": True,
}

#: The basis vocabulary. An ALLOWLIST, deliberately: an unrecognised value in the column reads as
#: UNDECLARED and falls back to the house default, rather than being passed through to arithmetic
#: that would then do something nobody asked for. Same posture as
#: `carrier_dollar_class.component_is_evidence` (index §66).
STRETCH_BASES = ("achieved", "target")

#: Units whose goal is money and therefore rounds to the cent. Anything else is a TALLY and rounds
#: to a whole unit — asked of the category's unit rather than special-casing the one category that
#: happens to be money today. `targets_engine.target_units` is where a category's unit is declared.
MONEY_UNITS = ("dollars",)

#: The floor on `stretch_factor` (see HOUSE_DEFAULT above).
MIN_STRETCH_FACTOR = 1.0


def _num(v, default=None):
    """`v` as a float, or `default` when it is absent or not a number. NULL is not a declaration."""
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):      # NaN / inf are not declarations either
        return default
    return f


def policy_for(rows, org_id: str) -> dict:
    """This tenant's roll-forward policy: its own declared row merged OVER the house default.

    `rows`   — every `commcalc.goal_rollforward_policy` row the caller read (any set; this function
               picks the one matching `org_id` and ignores the rest).
    `org_id` — REQUIRED. A caller that has not asked which tenant cannot get an answer, so one
               tenant's rate can never be reached from another tenant's request.

    MERGED PER FIELD, never all-or-nothing: a row that declares only a factor keeps the house basis
    and the house switch. A NULL column is the absence of a declaration, not a declaration of
    nothing — otherwise adding a column to this table would silently blank every existing row's
    behaviour. An unrecognised basis, or a factor below 1.0, is treated as undeclared.

    A tenant with NO row of its own gets the HOUSE DEFAULT — never another tenant's row, and never
    "no policy at all". That is the leakage the 2026-10-30 multi-tenant directive is about.
    """
    out = dict(HOUSE_DEFAULT)
    want = str(org_id or "")
    if not want:
        raise ValueError("policy_for needs an org_id — a roll-forward policy is per tenant")
    row = None
    for r in (rows or []):
        # EXACT match. A loose compare (strip / casefold) is how one tenant's row reaches another's
        # read, and an org_id is a UUID whose spelling is not the tenant's to vary.
        if str((r or {}).get("org_id") or "") == want:
            row = r
            break
    if not row:
        return out

    basis = str(row.get("stretch_basis") or "").strip().lower()
    if basis in STRETCH_BASES:
        out["stretch_basis"] = basis

    factor = _num(row.get("stretch_factor"))
    if factor is not None and factor >= MIN_STRETCH_FACTOR:
        out["stretch_factor"] = factor

    auto = row.get("auto_fill")
    if auto is not None:
        out["auto_fill"] = bool(auto)
    return out


def rounded(value, *, unit: str):
    """`value` in the CATEGORY'S OWN UNIT — money to the cent, anything else to a whole unit.

    The unit is asked, never assumed: a tenant's declared category can be a tally (drop-offs) or
    money (printing revenue), and `targets_engine.target_units` is where that is declared. An
    unrecognised unit rounds WHOLE rather than to cents — a goal is counted unless it is declared to
    be money, so a new unit cannot accidentally start carrying fractional targets."""
    if value is None:
        return None
    return round(float(value), 2) if unit in MONEY_UNITS else int(round(float(value)))


def next_goal(prior_target, achieved, policy: dict, *, unit: str) -> dict:
    """Next month's goal for ONE store and ONE category.

    Returns `{'value', 'basis'}` where basis is:
      'stretch' — the goal was MET, so the new goal is `factor` x the basis figure
      'carry'   — the goal was MISSED, so the same number carries forward unchanged
      'new'     — there was no prior goal, so there is nothing to roll. `value` is **None**, never
                  0: a zero would be a goal of zero that no human set, and a plausible-looking
                  number is how this defect class hides.

    'MET' is `achieved >= prior_target`, boundary INCLUDED — hitting the number exactly is meeting
    it. A goal of zero or less is the ABSENCE of a goal, so beating it is not an achievement to
    stretch from. A missing achievement against a real goal is a MISS (carry), not a crash: the
    store is not credited with a result the feed does not show.

    `unit` is REQUIRED and decides the rounding (see `rounded`).
    """
    tgt = _num(prior_target, 0.0) or 0.0
    got = _num(achieved)            # None = not measured, which is NOT zero achievement...
    if tgt <= 0:
        return {"value": None, "basis": "new"}
    if got is not None and got >= tgt:
        base = got if policy.get("stretch_basis") == "achieved" else tgt
        factor = _num(policy.get("stretch_factor"), HOUSE_DEFAULT["stretch_factor"])
        return {"value": rounded(base * factor, unit=unit), "basis": "stretch"}
    # ...but against a goal that exists it is a miss, and the goal carries forward unchanged.
    return {"value": rounded(tgt, unit=unit), "basis": "carry"}


def auto_fill_enabled(rows, org_id: str) -> bool:
    """Whether this tenant's next-month goals are written without anyone pressing anything."""
    return bool(policy_for(rows, org_id).get("auto_fill"))
