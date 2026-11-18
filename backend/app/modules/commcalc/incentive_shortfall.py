"""§COMMCALC-81 — HOW CLOSE WAS THIS DAY TO THE PAYOUT, AND WHAT DID MISSING IT COST?

THE CLASS. The wrong general fact this fixes:

    "a day that missed its qualifying threshold earned nothing, and that is the whole story."

A gate is binary. A SHORTFALL IS NOT. A day two sales short of a five-sale gate and a day that
sold nothing both pay $0.00, and every surface here showed them as the same zero — so the one of
them that is a coachable near miss was indistinguishable from the one that is just a quiet day.

Owner, 2026-11-18, on his own store's October: *"the reps have done shipments but not the overnight
or second day — if they did one or two more shipments for overnight they would have gotten paid for
all the overnights they have done, we need to drive that behavior with constant coaching."*

That last clause is the whole design. A threshold gate does not withhold the payout on the units you
were short of; it withholds it on EVERY UNIT YOU ALREADY DID. The work is already done and unpaid.
That is an argument a rep can act on tomorrow, and it is arithmetic, not exhortation.

WHY THE FORGONE FIGURE IS NOT COMPUTED HERE. The amount a failed gate cost is "what this day would
have paid with that one gate open" — so this module ASKS THE ENGINE, by handing `daily_incentive`
the same day with that rule's qualifier removed, and taking the difference. There is deliberately no
payout arithmetic in this file. A second copy of "what a rule pays" is the duplicate defect the index
rules forbid, and it would drift the first moment a rule kind, a cap or a split basis changed.

WHAT IS NOT A NEAR MISS, and this is the half that matters:

  · A GATE THAT COULD NOT BE VERIFIED. `daily_incentive._gate` fails closed when a qualifier names a
    category the business does not declare, or carries a comparison nobody knows. That is a
    CONFIGURATION DEFECT, and coaching a rep for it blames a person for an admin's unfinished setup.
    Those come back as `blocked` items addressed to whoever can fix the config, never to the rep.
  · A GATE NOBODY COULD HAVE CLEARED. A day miles below its threshold is not a coaching moment about
    one more sale; saying "two more and you'd have been paid" when the truth is forty more is the
    kind of false encouragement that teaches reps to ignore the whole surface. How close counts as
    within reach is a declared POLICY (see below), and an out-of-reach miss is reported `distant` —
    still shown, never dressed up as nearly there.
  · A CEILING GATE. A qualifier that caps rather than floors (`lte` / `lt` / `eq`) is not missed BY
    DOING TOO LITTLE, so "one or two more" is the wrong advice and this module declines to give it.

THE POLICY, and why a house default is legitimate here. "Within reach" is a rate, a window, a
switch — it names no category, no carrier, no product and no tenant — so under the 2026-10-30
directive it is a POLICY and a house default is right for it. The thing that directive BANS is a
default that is really one tenant's vocabulary, and there is none in this file: every category word
on an item is read out of the tenant's own catalog through `metric_catalog.by_key`.

PURE. DB-free. No tenant, carrier or product name appears here (RULE TWO).
"""
from app.modules.closing import metric_catalog as mc

# ── The policy: how close is "within reach" ───────────────────────────────────────────────────────
# Either test can carry a miss: an absolute allowance, because "two more sales" is how a shortfall
# is actually felt on a small gate, and a proportional one, because two more is meaningless on a gate
# of four hundred. Both are policy, both have house defaults, and a per-org row overrides either.
REACH_UNITS_DEFAULT = 2.0
REACH_FRACTION_DEFAULT = 0.15

# The item vocabulary, shared with the action plan so the two surfaces cannot disagree (§59.7).
METRIC_LABEL = "incentive_shortfall"
SEVERITY_NEAR = "critical"      # within reach, and the work is already done and unpaid
SEVERITY_DISTANT = "warning"    # a real miss, honestly out of reach for one more sale
SEVERITY_BLOCKED = "blocked"    # the gate could not be verified — an admin's item, not a rep's
KIND_NEAR, KIND_DISTANT, KIND_BLOCKED = "near_miss", "distant", "blocked"

# A gate is "missed by doing too little" only when it floors. See the docstring.
FLOOR_OPS = ("gte", "gt")


def _num(v, d=0.0):
    try:
        return float(d) if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return float(d)


def _key(v):
    return str(v or "").strip()


def _money(v):
    return round(_num(v) + 0.0, 2)


# ── What was short, and in which unit ─────────────────────────────────────────────────────────────

def shortfall_of(gate_detail):
    """One failed qualifier verdict → how far short it fell, or None when that question is wrong.

    Takes `daily_incentive._gate`'s own detail — never a re-evaluated gate, so this cannot disagree
    with the verdict the money was decided by. PURE.

    Returns None for a gate that PASSED, for a ceiling gate, and for a gate that could not be
    verified at all (`missing`, or a comparison with no `basis` on the verdict). `blocked_reason`
    tells those last ones apart for a caller: see `classify`.

    `short_by` is in the gate's OWN unit — whole units for a unit gate, dollars otherwise — because
    "you were 1.0 sales short" is not a sentence anybody acts on.
    """
    d = gate_detail or {}
    if d.get("passed") is not False:
        return None
    basis = _key(d.get("basis")).lower()
    op = _key(d.get("op")).lower()
    if not basis or op not in FLOOR_OPS:
        return None
    if d.get("missing"):
        # A gate carrying BOTH a measured basis and an undeclared category is still unverifiable: the
        # figure it measured is a PARTIAL one, so "two more and you'd have been paid" is not knowable.
        # `blocked_reason` is what answers such a verdict. Caught by arming the proof harness.
        return None
    value, threshold = _num(d.get("value")), _num(d.get("threshold"))
    short = threshold - value
    if op == "gt":
        # A strict floor is cleared by exceeding it, so matching it exactly is still short. One more
        # whole unit on a unit gate; on money, the smallest amount we can represent.
        short = short + (1.0 if basis == "units" else 0.01)
    if short <= 0:
        return None
    if basis == "units":
        short = float(int(short) + (1 if short > int(short) else 0))   # ceil: part of a sale is one whole sale
    else:
        short = _money(short)
    return {"basis": basis, "op": op, "scope": _key(d.get("scope")).lower() or "none",
            "value": (int(value) if basis == "units" else _money(value)),
            "threshold": (int(threshold) if basis == "units" else _money(threshold)),
            "short_by": (int(short) if basis == "units" else short),
            "unit": ("units" if basis == "units" else "money"),
            "metric_keys": [_key(k) for k in (d.get("metric_keys") or []) if _key(k)]}


def blocked_reason(gate_detail):
    """Why a failed gate cannot be read as a shortfall at all, or "" when it can. PURE.

    A gate that fails CLOSED because the config is unfinished is an admin's problem. Returning the
    reason rather than silently dropping it is what stops this surface quietly under-reporting the
    days nobody was paid for."""
    d = gate_detail or {}
    if d.get("passed") is not False:
        return ""
    if d.get("missing"):
        return ("the qualifier names a category this business does not declare, so the gate could "
                "not be checked at all")
    if not _key(d.get("basis")):
        return _key(d.get("reason")) or "the gate could not be evaluated"
    return ""


def within_reach(short, *, reach_units=None, reach_fraction=None):
    """Is this shortfall close enough that one or two more sales was the whole difference? PURE.

    Either test carries it. `None` for a policy argument means the house default — this is a POLICY
    (a window), which is exactly where a house default stays legitimate under the 2026-10-30
    directive, and the directive's ban is on defaults that are really a tenant's VOCABULARY."""
    s = short or {}
    by = _num(s.get("short_by"))
    if by <= 0:
        return False
    units = _num(reach_units, REACH_UNITS_DEFAULT) if reach_units is not None else REACH_UNITS_DEFAULT
    frac = (_num(reach_fraction, REACH_FRACTION_DEFAULT) if reach_fraction is not None
            else REACH_FRACTION_DEFAULT)
    if s.get("basis") == "units" and by <= units:
        return True
    thr = _num(s.get("threshold"))
    return bool(thr > 0 and frac > 0 and (by / thr) <= frac)


# ── Asking the engine what the gate cost ──────────────────────────────────────────────────────────

def opened_rule(rule):
    """The same rule with its qualifier OPENED — the counterfactual, built in one place. PURE.

    Only the qualifier is touched. Rate, cap, basis, categories and split basis are carried through
    untouched, so the engine answers the question "what would this have paid" about the rule the
    tenant actually declared, and not about a simplified version of it."""
    r = dict(rule or {})
    r["qualifier"] = {"scope": "none"}
    return r


def _paid_total(result):
    return _money(sum(_num(l.get("amount")) for l in ((result or {}).get("lines") or [])))


def _verdicts_of(result):
    """Every qualifier verdict a one-rule run produced: the store's, plus each person's.

    A verdict that PASSED is still returned — `shortfall_of` is what discards it, so there is one
    reader of "did this gate fail" and not two. And a rule can produce passed AND failed verdicts on
    the same day, which is why there is no "this rule paid, so there is nothing to coach" shortcut
    here: an employee-scope gate one person cleared and another missed is exactly the individual near
    miss this module is for, and that shortcut silently swallowed it (caught by arming the proof
    harness, 2026-11-18)."""
    out = []
    for note in ((result or {}).get("rules") or []):
        q = note.get("qualifier")
        if isinstance(q, dict):
            out.append((None, q))
        for who, det in (note.get("per_employee_qualifier") or {}).items():
            if isinstance(det, dict):
                out.append((_key(who), det))
    return out


def rule_shortfall(engine, catalog, rule, day, *, reach_units=None, reach_fraction=None):
    """ONE rule against ONE store-day → its shortfall item, or None when it has none. PURE.

    `engine` is `daily_incentive.compute_day` — passed in rather than imported so that the engine
    never depends on this module and the dependency runs one way only. `day` is the keyword bag that
    engine takes (`declared_map`, `per_employee`, `hours_by_employee`, `employees`, `unclassified`,
    `unclassified_by_employee`); it is handed through UNCHANGED, so the counterfactual is measured on
    the identical day under the identical revenue scope as the real run.

    The forgone amount is the engine's own difference, never arithmetic from this file."""
    as_is = engine(catalog, [rule], **day)
    verdicts = _verdicts_of(as_is)
    if not verdicts:
        return None
    blocked = next(((w, b) for w, d in verdicts for b in [blocked_reason(d)] if b), None)
    shorts = [(w, s) for w, d in verdicts for s in [shortfall_of(d)] if s]
    if not shorts and not blocked:
        return None
    opened = engine(catalog, [opened_rule(rule)], **day)
    forgone = _money(_paid_total(opened) - _paid_total(as_is))
    label = _key(rule.get("label")) or _key(rule.get("rule_key")) or _key(rule.get("rule_kind"))
    base = {"rule": label, "rule_id": _key(rule.get("id")), "rule_kind": _key(rule.get("rule_kind")),
            "forgone": forgone, "would_have_paid": {k: _money(v) for k, v in
                                                    (opened.get("by_employee") or {}).items()}}
    if blocked:
        return {**base, "kind": KIND_BLOCKED, "severity": SEVERITY_BLOCKED,
                "employee": blocked[0], "reason": blocked[1], "shortfall": None}
    # The tightest miss is the one to coach: it is the one a single extra sale could have closed.
    who, short = min(shorts, key=lambda ws: _num(ws[1].get("short_by")))
    near = within_reach(short, reach_units=reach_units, reach_fraction=reach_fraction)
    return {**base, "kind": (KIND_NEAR if near else KIND_DISTANT),
            "severity": (SEVERITY_NEAR if near else SEVERITY_DISTANT),
            "employee": who, "reason": "", "shortfall": short,
            "did": _did(catalog, short, day),
            "within_reach": near}


def _did(catalog, short, day):
    """What the day DID do in the categories the gate counts — the "everything you have
    already done" half of the owner's sentence. Words come from the TENANT's own catalog. PURE."""
    by_key = mc.by_key(catalog)
    declared = (day or {}).get("declared_map") or {}
    out = []
    for k in (short.get("metric_keys") or []):
        m = by_key.get(k)
        if not m:
            continue
        out.append({"key": k, "label": _key(m.get("label")) or k,
                    "units": mc.units_of(declared, k), "amount": _money(mc.amount_of(declared, k))})
    return out


def day_shortfalls(engine, catalog, rules, day, *, reach_units=None, reach_fraction=None):
    """Every unpaid rule on one store-day, worst-cost first. PURE.

    An inactive rule is skipped, because a rule nobody is on cannot be missed."""
    out = []
    for rule in (rules or []):
        if (rule or {}).get("is_active") is False:
            continue
        item = rule_shortfall(engine, catalog, rule, day, reach_units=reach_units,
                              reach_fraction=reach_fraction)
        if item:
            out.append(item)
    out.sort(key=lambda i: (-_num(i.get("forgone")), _key(i.get("rule"))))
    return out


# ── The words, and the guided flow ────────────────────────────────────────────────────────────────

def _plural(n, one, many):
    return one if abs(_num(n)) == 1 else many


def _fig(short, v):
    return (f"{int(_num(v)):,}" if short.get("basis") == "units" else f"${_money(v):,.2f}")


def coach_sentence(item):
    """The coaching sentence for one item — the owner's argument, said with the numbers that make it
    arguable rather than as an exhortation. "" when the item cannot support one. PURE."""
    i = item or {}
    if i.get("kind") == KIND_BLOCKED:
        return (f"{_key(i.get('rule'))} paid nothing and cannot say whether it should have: "
                f"{_key(i.get('reason'))}. An admin needs to finish this rule before anybody is "
                f"coached on it.")
    s = i.get("shortfall") or {}
    if not s:
        return ""
    by, forgone = s.get("short_by"), _money(i.get("forgone"))
    did = [d for d in (i.get("did") or []) if _num(d.get("units")) or _num(d.get("amount"))]
    done = ", ".join(f"{int(_num(d.get('units'))):,} {_key(d.get('label'))}" for d in did
                     if _num(d.get("units")))
    unit = _plural(by, "sale", "sales") if s.get("basis") == "units" else "of sales"
    head = (f"{_fig(s, by)} more {unit} would have cleared {_key(i.get('rule'))}"
            if s.get("basis") == "units" else
            f"{_fig(s, by)} more would have cleared {_key(i.get('rule'))}")
    tail = (f" That gate does not withhold the pay on the {_fig(s, by)} that were missing — it "
            f"withholds it on everything already sold, so the {done} on the day went unpaid."
            if done else
            " That gate withholds the pay on everything already sold, not only on the part that "
            "was missing.")
    cost = (f" It cost ${forgone:,.2f}." if forgone > 0 else
            " The rule pays nothing on this day even with the gate open, so the gate is not what "
            "cost the money.")
    reach = ("" if i.get("within_reach") else
             f" This one was {_fig(s, by)} short of {_fig(s, s.get('threshold'))}, which is further "
             f"than one or two more sales — worth planning for, not worth promising.")
    return head + "." + tail + cost + reach


def steps(item):
    """The guided flow, AS DATA — one step per thing the rep has to understand, in order. PURE.

    This is deliberately not prose with numbers glued in: a surface renders the figure as a figure,
    draws the bar, and never has to re-derive what the step is about. An empty list means the item
    cannot be walked through, which a surface must treat as "show the sentence only"."""
    i = item or {}
    s = i.get("shortfall") or {}
    if i.get("kind") == KIND_BLOCKED or not s:
        return []
    did = [d for d in (i.get("did") or []) if _num(d.get("units")) or _num(d.get("amount"))]
    value, thr, by = s.get("value"), s.get("threshold"), s.get("short_by")
    out = [
        {"n": 1, "step": "did", "title": "What you already sold",
         "figure": value, "unit": s.get("unit"), "of": thr,
         "detail": [{"label": _key(d.get("label")), "units": int(_num(d.get("units"))),
                     "amount": _money(d.get("amount"))} for d in did],
         "line": (f"The day counted {_fig(s, value)} toward {_key(i.get('rule'))}.")},
        {"n": 2, "step": "gate", "title": "What the rule needed",
         "figure": thr, "unit": s.get("unit"), "of": thr,
         "line": (f"It pays once the day reaches {_fig(s, thr)}"
                  + (" at this store." if s.get("scope") == "store" else " for one person."))},
        {"n": 3, "step": "gap", "title": "How far short you were",
         "figure": by, "unit": s.get("unit"), "of": thr,
         "line": (f"{_fig(s, by)} short." if s.get("basis") != "units"
                  else f"{_fig(s, by)} {_plural(by, 'sale', 'sales')} short.")},
        {"n": 4, "step": "cost", "title": "What that cost",
         "figure": _money(i.get("forgone")), "unit": "money", "of": None,
         "line": (f"${_money(i.get('forgone')):,.2f} — and not on the {_fig(s, by)} that were "
                  f"missing. On everything already sold."
                  if _money(i.get("forgone")) > 0 else
                  "Nothing, on this day: the rule pays nothing here even with the gate open.")},
    ]
    if i.get("within_reach"):
        out.append({"n": 5, "step": "next", "title": "What to do tomorrow",
                    "figure": by, "unit": s.get("unit"), "of": thr,
                    "line": (f"Ask for it {_fig(s, by)} more {_plural(by, 'time', 'times')} and "
                             f"everything else you sell that day gets paid too."
                             if s.get("basis") == "units" else
                             f"{_fig(s, by)} more on the day turns the whole day paid.")})
    return out


def action_item(item):
    """ONE shortfall → the action plan's own item shape, or None. PURE.

    Mirrors `peer_comparison.peer_action_item` deliberately: the action plan already carries items of
    that shape, and giving this one its own shape would make two kinds of item on one plan."""
    i = item or {}
    sentence = coach_sentence(i)
    if not sentence:
        return None
    s = i.get("shortfall") or {}
    thr = _num(s.get("threshold"))
    pct = round((_num(s.get("short_by")) / thr) * 100.0, 1) if thr > 0 else None
    title = (f"{_key(i.get('rule'))} — {_fig(s, s.get('short_by'))} short of being paid"
             if s else f"{_key(i.get('rule'))} — cannot be checked")
    return {"severity": _key(i.get("severity")), "metric": METRIC_LABEL,
            "title": title, "detail": sentence, "shortfall_pct": pct,
            "incentive": {"rule": _key(i.get("rule")), "rule_id": _key(i.get("rule_id")),
                          "kind": _key(i.get("kind")), "employee": i.get("employee"),
                          "forgone": _money(i.get("forgone")), "shortfall": s or None,
                          "did": i.get("did") or [], "steps": steps(i)}}


# ── Rolling days up to the period the coaching card is about ─────────────────────────────────────

def fold_days(days):
    """[{date, store_code, items[]}] → what a period of them says, per rule and per person. PURE.

    The coaching card is period-grain and a gate is day-grain, so somebody has to fold. Doing it here
    means the card, the action plan and the daily screen cannot disagree about how many days were
    missed or what they cost — which is the §59.7 rule applied to this question.

    `closest` is the day a single extra sale would most nearly have rescued, which is the one worth
    opening the walkthrough on."""
    by_rule, by_emp, blocked = {}, {}, []
    total, near_total, days_missed = 0.0, 0.0, 0
    closest = None
    for d in (days or []):
        items = (d or {}).get("items") or []
        if items:
            days_missed += 1
        for it in items:
            stamped = {**it, "date": _key(d.get("date")), "store_code": _key(d.get("store_code"))}
            if it.get("kind") == KIND_BLOCKED:
                blocked.append(stamped)
                continue
            f = _money(it.get("forgone"))
            total += f
            r = by_rule.setdefault(_key(it.get("rule")), {
                "rule": _key(it.get("rule")), "rule_id": _key(it.get("rule_id")),
                "days": 0, "days_within_reach": 0, "forgone": 0.0, "forgone_within_reach": 0.0})
            r["days"] += 1
            r["forgone"] = _money(r["forgone"] + f)
            who = _key(it.get("employee"))
            if who:
                e = by_emp.setdefault(who, {"employee": who, "days": 0, "days_within_reach": 0,
                                            "forgone": 0.0})
                e["days"] += 1
                e["forgone"] = _money(e["forgone"] + f)
            if it.get("within_reach"):
                near_total += f
                r["days_within_reach"] += 1
                r["forgone_within_reach"] = _money(r["forgone_within_reach"] + f)
                if who:
                    by_emp[who]["days_within_reach"] += 1
                s = it.get("shortfall") or {}
                cur = (closest or {}).get("shortfall") or {}
                if not closest or _num(s.get("short_by")) < _num(cur.get("short_by")) \
                        or (_num(s.get("short_by")) == _num(cur.get("short_by"))
                            and f > _money(closest.get("forgone"))):
                    closest = stamped
    return {"days_missed": days_missed,
            "forgone": _money(total), "forgone_within_reach": _money(near_total),
            "by_rule": sorted(by_rule.values(), key=lambda r: -_num(r.get("forgone"))),
            "by_employee": sorted(by_emp.values(), key=lambda e: -_num(e.get("forgone"))),
            "blocked": blocked, "closest": closest}


def period_sentence(folded):
    """The one line the coaching card leads with. "" when there is nothing to say. PURE."""
    f = folded or {}
    days, near = int(_num(f.get("days_missed"))), _money(f.get("forgone_within_reach"))
    total = _money(f.get("forgone"))
    if not days or total <= 0:
        return ""
    head = (f"{days} {_plural(days, 'day', 'days')} earned nothing from a rule that was gated, "
            f"and those gates cost ${total:,.2f}.")
    if near <= 0:
        return head + " None of them was close enough that one more sale would have changed it."
    pct = round((near / total) * 100.0) if total > 0 else 0
    return (head + f" ${near:,.2f} of that — {pct}% — was on days that were within one or two sales "
            f"of being paid in full, which is the part coaching can move.")
