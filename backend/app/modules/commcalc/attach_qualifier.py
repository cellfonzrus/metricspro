"""ATTACH QUALIFIER — the ONE home for "did this SALE earn the line its full rate, or the lesser one?"

Owner directive 2026-10-12 (verbatim): *"Payment for edge on total which is defined as $25 must be
accompanied with atleast $25 worth of accessories , add this logic in the system not as hard coded
but as a logic platform wide which might be used by other carriers also , label the commission as
$25 acc is must to be qualified for $25 commission otherwise the commision falls back to $10 or
whatever is paid to Chicago market"*

THE CLASS, NOT THE INSTANCE (owner directive 2026-09-20). The general fact that was missing is not
"Total's edge payout needs an accessory test". It is that **every gate in this engine can only turn a
payment OFF.** `plan_pay_gate` suppresses (scope, exclusion, unit dedup); `financing_tiers` replaces a
rate from a MONTH's attainment. Nothing could say "this line earns a LESSER rate because of a fact
about the SALE it belongs to". That is the mechanism, and it is carrier-agnostic: a condition measured
over the whole transaction decides which of two declared rates the line earns.

RULE TWO (config, never code). No carrier, tenant, product, market or dollar figure appears here. A
rule DECLARES its own condition with the same `match_field` / `match_op` / `match_value` vocabulary a
`commission_rule` already uses, so "which sibling lines count" is the tenant's sentence, not ours.

WHAT IS NOT DUPLICATED HERE
  · **"Is this line an accessory"** is NOT answered here. There are already ~8 accessory classifiers
    in this codebase (index §19.42 and the `accessory_definition` docstring), and the one the money
    path honours is `plan_pay_gate.accessory_predicate` → `accessory_definition.classify`, the map the
    owner curates by hand. It is INJECTED as `is_accessory`. A ninth classifier here would be the
    duplicate defect the index rules forbid.
  · **"Does this line match a rule"** is NOT answered here either. `commission_engine._rule_matches`
    is injected as `matches`, exactly as `financing_tiers.build_context` takes the same callable —
    "the store's unit count is produced by exactly the code that decides what pays (never a second
    copy of the matching logic)".

MEASURED OVER THE WHOLE SALE, NOT THE REP'S SHARE OF IT. "accompanied with at least $25 worth of
accessories" is a fact about what the customer bought, so the context is built from the period's whole
validated row list, not from one rep's lines. Two reps ringing different lines of one sale therefore
read the SAME verdict, which is the only answer that is not an accident of who touched the till.

AN UNMEASURABLE SALE PAYS THE FULL RATE, AND IS REPORTED. A row carrying no transaction id has no
knowable siblings, so its accessory total cannot be computed — it is NOT zero. Treating "unknown" as
"zero accessories" would quietly dock the rep on a data defect, which is the shape this house forbids
(a defect in live data is REPORTED, never hidden). Such a line earns the full rate and is counted in
`unmeasured`, the same posture `payroll_hours_basis` takes for an undated cell: keep today's answer
rather than push the row to one side of a boundary it never named.

ALLOWLIST POSTURE (§66). An unruled `measure` or `scope` reads as the house default rather than
becoming a third behaviour, and a rule with no `qualifier_min` has NO qualifier at all — so this
module is inert by construction until a tenant declares one, and every existing payout is
byte-identical.
"""

# ── the declared vocabulary ───────────────────────────────────────────────────────────────────────
MEASURE_AMOUNT = "amount"   # the selling price of the qualifying sibling lines (ext_price)
MEASURE_GP = "gp"           # their gross profit
MEASURE_COUNT = "count"     # how many of them there are

MEASURES = (MEASURE_AMOUNT, MEASURE_GP, MEASURE_COUNT)

#: "worth $25" is what the customer paid, not what the store kept — so price, not GP.
DEFAULT_MEASURE = MEASURE_AMOUNT

SCOPE_TRANSACTION = "transaction"
SCOPES = (SCOPE_TRANSACTION,)
DEFAULT_SCOPE = SCOPE_TRANSACTION

#: What a line earns when the sale did not qualify and the rule declares no fallback: nothing. The
#: line is still SHOWN with its reason — a $0 with no explanation is the defect, not the $0.
BASIS_FULL = "full"
BASIS_FALLBACK = "fallback"
BASIS_UNMEASURED = "unmeasured"
#: The rule declares a fallback its payout kind cannot express. Reported; nothing is changed.
BASIS_UNSUPPORTED = "unsupported"


def _f(v):
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _num_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(v):
    return str(v or "").strip().lower()


def resolve(rule):
    """The rule's declared qualifier, or **None** when it declares none.

    None is the whole inertness guarantee: every caller skips this module entirely for such a rule, so
    a tenant that has not declared a qualifier pays byte-identically to before. A `qualifier_min` of
    zero or below is also None — "at least nothing" is not a condition, and storing 0 to mean "off"
    must not become a second spelling of on.
    """
    rule = rule if isinstance(rule, dict) else {}
    minimum = _num_or_none(rule.get("qualifier_min"))
    if minimum is None or minimum <= 0:
        return None
    measure = _norm(rule.get("qualifier_measure"))
    scope = _norm(rule.get("qualifier_scope"))
    return {
        "minimum": minimum,
        "measure": measure if measure in MEASURES else DEFAULT_MEASURE,
        "scope": scope if scope in SCOPES else DEFAULT_SCOPE,
        # WHICH sibling lines count, in the rule matcher's own vocabulary.
        "match_field": rule.get("qualifier_match_field") or "accessory",
        "match_op": rule.get("qualifier_match_op") or "equals",
        "match_value": rule.get("qualifier_match_value") or "yes",
        # What the line earns when the sale falls short. None = nothing, stated rather than implied.
        "fallback_amount": _num_or_none(rule.get("qualifier_fallback_amount")),
        "label": str(rule.get("qualifier_label") or "").strip() or None,
    }


def txn_key(row):
    """A row's transaction id, spelled the one way this codebase spells it, or '' when it carries none."""
    return str((row or {}).get("trans_id", "") or "").replace(".0", "").strip()


def _counts_toward(row, q, is_accessory=None, matches=None):
    """Does this sibling line count toward the qualifier? The accessory test and the rule matcher are
    both INJECTED — this module owns neither."""
    field = _norm(q.get("match_field")) or "accessory"
    if field == "accessory":
        if is_accessory is None:
            return None            # cannot be answered; the caller reports it rather than guessing
        return bool(is_accessory(row))
    if matches is None:
        return None
    return bool(matches(row, {"match_field": q.get("match_field"),
                              "match_op": q.get("match_op"),
                              "match_value": q.get("match_value")}))


def _line_measure(row, measure):
    if measure == MEASURE_COUNT:
        return 1.0
    if measure == MEASURE_GP:
        return _f((row or {}).get("gp"))
    return _f((row or {}).get("ext_price"))


def build_context(all_rows, rules, is_accessory=None, matches=None):
    """The per-transaction totals every qualifying rule needs, measured ONCE over the whole period.

    `all_rows` is the period's validated feed — the whole sale, not one rep's share of it. Returns
    `{"active", "by_rule", "unmeasurable", "notes"}`; `active` is False when no rule declares a
    qualifier, and every caller then takes the pre-qualifier path untouched.
    """
    out = {"active": False, "by_rule": {}, "unmeasurable": 0, "notes": []}
    qualifying = []
    for rule in (rules or ()):
        q = resolve(rule)
        if q is not None:
            qualifying.append((str((rule or {}).get("id") or ""), q))
    if not qualifying:
        return out

    rows = list(all_rows or ())
    unmeasurable = sum(1 for r in rows if not txn_key(r))
    for rid, q in qualifying:
        totals = {}
        unanswerable = 0
        for r in rows:
            key = txn_key(r)
            if not key:
                continue
            hit = _counts_toward(r, q, is_accessory, matches)
            if hit is None:
                unanswerable += 1
                continue
            if hit:
                totals[key] = round(totals.get(key, 0.0) + _line_measure(r, q["measure"]), 2)
        # A predicate that could not be read at all makes this rule's condition UNANSWERABLE, and
        # an unanswerable condition must never read as "the sale carried nothing". Without this
        # flag every such sale measures 0.00 and silently takes the LESSER rate — the exact silent
        # dock this module's docstring forbids. Caught by this module's own harness (§B6).
        entry = {"q": q, "totals": totals, "unanswerable": unanswerable,
                 "predicate_unavailable": bool(unanswerable) and not totals}
        out["by_rule"][rid] = entry
        if unanswerable:
            # The accessory definition could not be read, so this rule's condition is unanswerable on
            # those lines. Reported, never silently failed closed: see verdict()'s unmeasured branch.
            out["notes"].append({
                "code": "qualifier_predicate_unavailable", "rule_id": rid, "lines": unanswerable,
                "note": ("This rule's qualifier counts lines the accessory definition decides, and that "
                         "definition could not be read, so the condition could not be tested on "
                         f"{unanswerable} line(s). Those lines earn the full rate and are listed here "
                         "rather than quietly paying the lesser one.")})
    out["active"] = True
    out["unmeasurable"] = unmeasurable
    return out


def verdict(row, rule, ctx):
    """`None` when this rule declares no qualifier; otherwise the per-line outcome.

    `{met, measured, minimum, measure, basis, amount, reason}` — `amount` is what this line earns, and
    `basis` says which record produced it, because a number without its basis is the §66 defect.
    """
    q = resolve(rule)
    if q is None:
        return None
    full = _f((rule or {}).get("amount"))
    rid = str((rule or {}).get("id") or "")
    # A FALLBACK IS A FLAT RATE, so it cannot be substituted into a percentage rule — `_line_payout`
    # prices those off `pct`, and overwriting `amount` there would change nothing while the screen
    # claimed a lesser rate was being paid. That silent disagreement is worse than the misconfiguration,
    # so it is REFUSED and reported, and the rule keeps paying exactly what it pays today.
    if q["fallback_amount"] is not None and _norm(rule.get("payout_kind")) not in ("", "flat_per_unit"):
        return {"met": None, "measured": None, "minimum": q["minimum"], "measure": q["measure"],
                "basis": BASIS_UNSUPPORTED, "amount": round(full, 2),
                "reason": ("This rule pays a percentage, and a fallback rate is a flat amount, so the "
                           "lesser rate cannot be applied to it. Nothing is changed here; declare the "
                           "fallback on a flat-rate rule, or state the condition without one.")}
    entry = ((ctx or {}).get("by_rule") or {}).get(rid)
    key = txn_key(row)
    if entry is None or not key or entry.get("predicate_unavailable"):
        return {"met": None, "measured": None, "minimum": q["minimum"], "measure": q["measure"],
                "basis": BASIS_UNMEASURED, "amount": round(full, 2),
                "reason": ("What else was on this sale could not be read — it carries no "
                           "transaction id, or the accessory definition was unavailable — so the "
                           "condition could not be tested. The full rate is paid rather than docking "
                           "the sale for a gap in the data.")}
    measured = round(_f(entry["totals"].get(key)), 2)
    if measured >= q["minimum"]:
        return {"met": True, "measured": measured, "minimum": q["minimum"], "measure": q["measure"],
                "basis": BASIS_FULL, "amount": round(full, 2),
                "reason": describe_met(q, measured)}
    fb = q["fallback_amount"]
    return {"met": False, "measured": measured, "minimum": q["minimum"], "measure": q["measure"],
            "basis": BASIS_FALLBACK, "amount": round(_f(fb), 2) if fb is not None else 0.0,
            "fallback_declared": fb is not None,
            "reason": describe_unmet(q, measured, full)}


def rule_view(rule, v):
    """The rule as the payout calculator should see it: the SAME rule with its rate substituted.

    A shallow copy, never a mutation — `financing_tiers` replaces a flat per-unit rate the same way,
    and the engine's own `_line_payout` stays the only code that turns a rate into dollars.
    """
    if not v or v.get("basis") in (BASIS_FULL, BASIS_UNMEASURED, BASIS_UNSUPPORTED):
        return rule
    out = dict(rule or {})
    out["amount"] = v.get("amount") or 0.0
    return out


# ── the label, built from the tenant's OWN numbers ────────────────────────────────────────────────
def _money(v):
    f = _f(v)
    return ("$%d" % round(f)) if abs(f - round(f)) < 0.005 else ("$%.2f" % f)


def describe(rule):
    """The one-sentence rule the owner asked to see on screen, DERIVED from the declared numbers.

    The owner's words were *"label the commission as $25 acc is must to be qualified for $25
    commission"*. Those dollars are this tenant's figures, so the sentence is built from the config —
    a tenant with other numbers reads their own sentence, and no figure is hard-coded (RULE TWO). A
    rule may override the whole sentence with `qualifier_label`.
    """
    q = resolve(rule)
    if q is None:
        return ""
    if q["label"]:
        return q["label"]
    full = _f((rule or {}).get("amount"))
    noun = _measure_noun(q)
    need = (("%d" % round(q["minimum"])) if q["measure"] == MEASURE_COUNT else _money(q["minimum"]))
    head = f"{need} of {noun} is required to qualify for the {_money(full)} commission"
    fb = q["fallback_amount"]
    if fb is None:
        return head + "; without it this line pays nothing."
    return head + f"; otherwise {_money(fb)}."


def _measure_noun(q):
    field = _norm(q.get("match_field")) or "accessory"
    if field == "accessory":
        return "accessories" if q["measure"] != MEASURE_COUNT else "accessory items"
    value = str(q.get("match_value") or "").strip()
    return value or field


def describe_met(q, measured):
    noun = _measure_noun(q)
    shown = (("%d" % round(measured)) if q["measure"] == MEASURE_COUNT else _money(measured))
    need = (("%d" % round(q["minimum"])) if q["measure"] == MEASURE_COUNT else _money(q["minimum"]))
    return f"This sale carried {shown} of {noun}, meeting the {need} required, so the full rate is paid."


def describe_unmet(q, measured, full):
    noun = _measure_noun(q)
    shown = (("%d" % round(measured)) if q["measure"] == MEASURE_COUNT else _money(measured))
    need = (("%d" % round(q["minimum"])) if q["measure"] == MEASURE_COUNT else _money(q["minimum"]))
    fb = q["fallback_amount"]
    tail = (f"so the {_money(fb)} rate is paid instead of {_money(full)}."
            if fb is not None else
            f"and this rule declares no lesser rate, so the {_money(full)} is not paid.")
    return f"This sale carried {shown} of {noun}, short of the {need} required, {tail}"
