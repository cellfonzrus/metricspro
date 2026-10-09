"""ACCESSORY TARGET ALLOCATION — a company accessory goal, split across stores on what each store's
own accessories-per-box history says it can carry.

OWNER ASK (2026-10-09, verbatim): *"as a company we can decide what is my company target for
accesories sales, we need a new report which decides how the tragets should be assigned for the stores
based on the historic performance of the stores, the report will have he columns for last 2 months of
sales in separate columns, projected sale this month, current target and extended target to meet the
company goal, the next column will calculate the propotionate sales target required to be achived by
the store based on thier accessories per box history and the actual total boxes sold, the targets could
be a fixed number or a % increase from last month or even a % decrease from last month, the report
should have all this as user defined ont opt of the page and , the suer should jave the option to assign
this target proportionately to all stores or selected stores from the from the dropdown multi select
menu"*.

THE QUESTION, in one line: **the company names ONE accessory number; who has to sell what to get
there, measured on what each store already demonstrates it can do per box it sells.**

Everything the platform already knew answered a different question. §5's Accessory Sales Targets page
TRACKS a target that somebody already typed — target vs achieved vs pace. Nothing anywhere DERIVED
the target itself from a company figure, which is why every `commcalc.targets.accessories_monthly` row
on the platform was hand-entered or carried forward ±10% by `_carry_forward_map`. This module is that
derivation and nothing else: it computes, it never stores, and the number it suggests is written
through the SAME target row the tracker reads, so there is no second place a store's accessory target
can live.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched in `docs/SYSTEM_DATA_FLOW_INDEX.md` before a line was written: §5 (Daily Targets & actuals,
incl. the Accessory Sales Targets page and `_carry_forward_map`), §3 (the sales report and the shared
cell aggregation), §59 (peer sales comparison — the other report keyed on boxes sold), §59.8 (the
box-count bucket guard), §60 (spiff impact — the other report that ranks stores on boxes), §19.28
(KPI targets), §16–18 (the table / endpoint / metric cross-references).

| the fact this module needs | the ONE home it DEREFERENCES (never re-derives) |
|---|---|
| accessory $ a store sold in a month (the TARGET basis: accessory sales + device set-up fee) | `router._compute_feed_actuals_py` → `_sales_cell_agg` (§5 / §3) — injected as rows; not one sale line is read in this file |
| boxes a store sold in a month | the same `box_count` off the same pass, which §59.8's `_box_txn` guard already de-duplicates |
| projected month-end accessory $ and boxes | `router._targets_trending_by_code` → `_exec_mtd` (§5) — the projection Executive MTD and the tracker already show, so three surfaces cannot disagree |
| the store's CURRENT accessory target | `commcalc.targets.accessories_monthly` (mig `006`) — the row §5's tracker reads and `PUT /targets/{period}` writes |
| which store is this, spelled any way | `router._store_code_resolver` (§5's and §59's resolver) |
| may this caller edit this store's target | `router._require_target_edit` (§5) — the same gate the single-store save uses |

**NEW here and nowhere else:** the accessories-per-box capacity measure, the company-goal vocabulary
(fixed / % up / % down over a stated basis), the proportional split with its fallback ladder, and the
largest-remainder rounding that makes the assignments SUM to the goal.

**NO NEW TABLE, NO MIGRATION.** A second place to keep a store's accessory target would be exactly the
"two paths answering one question" the index rules forbid, and it would drift from the tracker within a
month. The suggestion is computed on every load and only ever written into mig `006`'s row.

════════════════════════════════════════════════════════════════════════════════════════════════════
THE ARITHMETIC, STATED SO IT CAN BE ARGUED WITH
════════════════════════════════════════════════════════════════════════════════════════════════════
    rate    = (acc$ last month + acc$ month before) / (boxes last month + boxes month before)
    boxes   = projected boxes this month           (fallback: average of the two history months)
    CAPACITY= rate × boxes                          ← what this store is DEMONSTRATED to be able to do
    target  = goal_to_split × capacity / Σ capacity  ← the proportional share
    extend  = target − current_target                ← the column the owner called "extended target"

Two months, not one, because a single month of accessory attachment is noisy and the rate is the whole
weight. The rate is history and the box count is the future, on purpose: a store's ATTACHMENT is a
property of how it sells, while its TRAFFIC is a property of this month — so a store whose boxes are
up carries more of the goal at the same attachment rate.

HONESTY RULES (each one is a check in `backend/harness_accessory_target_plan.py`):
  · **A goal nobody entered is not zero.** No figure typed → `goal: None`, every suggestion `None`,
    and the reason said. A report that silently plans to $0 is worse than one that plans nothing.
  · **A % of nothing is not a number.** "+10% on last month" with no last month measured is
    `no_basis`, never `goal: 0`.
  · **Zero attachment is a finding, not a weight of zero.** A store that sold boxes and attached no
    accessories is precisely the store a target is for; weighting it at zero would hand it a $0 target
    and call that planning. It is weighted at the COMPANY's own rate, and the row SAYS SO
    (`company_rate_zero_attach`) so nobody mistakes the suggestion for a measurement.
  · **No history is not no capacity.** A new store with no box history is weighted at the company rate
    on its projected boxes (`company_rate_no_history`), and said.
  · **An unselected store is never silently re-planned.** Its current target is RESERVED out of the
    goal and only the remainder is split, so assigning to three stores does not quietly assume the
    other twenty-five will do the rest.
  · **A goal already committed is reported, never forced.** When the unselected stores' current
    targets already exceed the whole goal, nothing is suggested and the overage is named — rather than
    emitting a negative target or clamping to zero and looking deliberate.
  · **The assignments SUM to the goal.** Proportional shares rounded independently miss by dollars;
    the largest-remainder pass puts the rounding residue on the largest shares, so the column foots.
  · **Nothing here writes anything.** The write is the caller's, through §5's existing gated endpoint.

NOT MONEY. An accessory target is a sales goal; `targets_engine.achieved_for_cat` pays on
prem/byod/upg/acc actuals and never reads a target, so no suggestion on this page can move a payout.

PURE. Every input is handed in — the history rows, the projection, the current targets, the goal the
user typed — so `backend/harness_accessory_target_plan.py` drives the whole module DB-free under
`env -i`.
"""

# The goal vocabulary, offered as a list so the UI picks rather than types (§3b's posture). Every
# member is a shape of arithmetic, not a carrier/tenant/product name (RULE TWO).
GOAL_MODES = ("fixed", "pct_increase", "pct_decrease")
GOAL_MODE_LABELS = {
    "fixed": "A fixed company total ($)",
    "pct_increase": "% increase over the basis",
    "pct_decrease": "% decrease from the basis",
}

# What a % is a percentage OF. Named, because "+10%" over a projection and over last month's actual are
# different goals and a report that does not say which is unauditable.
GOAL_BASES = ("last_month_actual", "two_month_average", "projected_this_month", "current_targets")
GOAL_BASIS_LABELS = {
    "last_month_actual": "Last month's actual accessory sales",
    "two_month_average": "Average of the last two months' actuals",
    "projected_this_month": "Projected month-end accessory sales",
    "current_targets": "The accessory targets currently set",
}

DEFAULT_GOAL_MODE = "pct_increase"
DEFAULT_GOAL_BASIS = "last_month_actual"

# Why a store carries the weight it does. Returned on every row; the UI shows it, so a suggestion
# resting on the company rate can never be read as a measurement of that store.
RATE_BASES = ("own_history", "company_rate_zero_attach", "company_rate_no_history", "no_basis")
BOX_BASES = ("projection", "history_average", "none")


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _i(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _r2(v):
    return round(_f(v), 2)


def _blank(v):
    """True when the user typed nothing. `0` is NOT nothing — a deliberate zero goal is a goal."""
    return v is None or str(v).strip() == ""


# ── THE COMPANY GOAL — one number, and the basis it rests on ──────────────────────────────────────
def company_goal(mode=None, value=None, basis=None, basis_amounts=None):
    """The company's accessory goal for the month → a dict that always NAMES where it came from. PURE.

    `basis_amounts` is {basis_key: measured $} handed in by the caller (last month's actual, the
    two-month average, the projection, the sum of current targets). `value` is what the user typed:
    dollars for `fixed`, a percentage for the two % modes.

    Returns `goal: None` with a `reason` whenever the goal cannot be computed — never a 0 standing in
    for "nothing entered", which is the rule §60 and §59 already keep for a percentage with no
    denominator.
    """
    mode = str(mode or DEFAULT_GOAL_MODE).strip().lower()
    if mode not in GOAL_MODES:
        return {"goal": None, "mode": mode, "basis": None, "basis_amount": None,
                "value": None, "reason": "unknown_mode"}
    basis = str(basis or DEFAULT_GOAL_BASIS).strip().lower()
    if basis not in GOAL_BASES:
        basis = DEFAULT_GOAL_BASIS
    amounts = dict(basis_amounts or {})
    basis_amount = _r2(amounts.get(basis)) if amounts.get(basis) is not None else None

    if _blank(value):
        return {"goal": None, "mode": mode, "basis": basis, "basis_amount": basis_amount,
                "value": None, "reason": "no_goal_entered"}
    v = _f(value)

    if mode == "fixed":
        # A fixed total needs no basis at all; the basis is still reported so the page can show what
        # the typed number is being compared against.
        return {"goal": _r2(max(0.0, v)), "mode": mode, "basis": basis,
                "basis_amount": basis_amount, "value": _r2(v),
                "reason": "negative_clamped_to_zero" if v < 0 else None}

    if not basis_amount:
        return {"goal": None, "mode": mode, "basis": basis, "basis_amount": basis_amount,
                "value": _r2(v), "reason": "no_basis"}
    pct = abs(v)
    factor = (1.0 + pct / 100.0) if mode == "pct_increase" else (1.0 - pct / 100.0)
    goal = basis_amount * factor
    # A decrease over 100% is a typo, not an instruction to plan negative sales.
    reason = "decrease_over_100_clamped_to_zero" if (mode == "pct_decrease" and pct > 100) else None
    return {"goal": _r2(max(0.0, goal)), "mode": mode, "basis": basis,
            "basis_amount": basis_amount, "value": _r2(pct), "reason": reason}


def basis_amounts(stores):
    """The measured $ behind each goal basis, summed over EVERY store in the window. PURE.

    Summed over every store and not only the selected ones on purpose: the company goal is the
    company's, and it must not move when somebody narrows the dropdown.
    """
    rows = list(stores or ())
    m1 = sum(_f(s.get("m1_acc")) for s in rows)
    m2 = sum(_f(s.get("m2_acc")) for s in rows)
    return {
        "last_month_actual": _r2(m1),
        "two_month_average": _r2((m1 + m2) / 2.0),
        "projected_this_month": _r2(sum(_f(s.get("projected_acc")) for s in rows)),
        "current_targets": _r2(sum(_f(s.get("current_target")) for s in rows)),
    }


# ── WHAT EACH STORE CAN CARRY — accessories per box × the boxes it will sell ───────────────────────
def company_rate(stores):
    """The company's own accessories-per-box rate over the two history months, or None. PURE.

    The fallback weight for a store its own history cannot speak for. Measured over the whole window,
    so it is the tenant's rate and not a number in code.
    """
    acc = sum(_f(s.get("m1_acc")) + _f(s.get("m2_acc")) for s in (stores or ()))
    boxes = sum(_i(s.get("m1_boxes")) + _i(s.get("m2_boxes")) for s in (stores or ()))
    if boxes <= 0 or acc <= 0:
        return None
    return round(acc / boxes, 4)


def capacity_row(store, house_rate=None):
    """ONE store's accessories-per-box rate, expected boxes and capacity, each with its basis. PURE.

    The fallback ladder, in order, and every rung NAMES itself on the row:
      own_history              — the store sold boxes and attached accessories to them.
      company_rate_zero_attach — it sold boxes and attached nothing. The finding, weighted at the
                                 company's rate so it gets a target instead of a $0 that looks
                                 deliberate.
      company_rate_no_history  — no boxes in either history month (a new or reopened store).
      no_basis                 — that, AND the company has no rate either (an empty window). Capacity
                                 is None and the row says so; it is never silently 0.
    """
    s = dict(store or {})
    hist_acc = _r2(_f(s.get("m1_acc")) + _f(s.get("m2_acc")))
    hist_boxes = _i(s.get("m1_boxes")) + _i(s.get("m2_boxes"))

    if hist_boxes > 0 and hist_acc > 0:
        rate, rate_basis = round(hist_acc / hist_boxes, 4), "own_history"
    elif hist_boxes > 0:
        rate, rate_basis = house_rate, "company_rate_zero_attach"
    else:
        rate, rate_basis = house_rate, "company_rate_no_history"
    if rate is None:
        rate_basis = "no_basis"

    proj_boxes = _i(s.get("projected_boxes"))
    if proj_boxes > 0:
        boxes, box_basis = proj_boxes, "projection"
    elif hist_boxes > 0:
        boxes, box_basis = round(hist_boxes / 2.0, 2), "history_average"
    else:
        boxes, box_basis = 0.0, "none"

    cap = None if (rate is None or not boxes) else _r2(rate * boxes)
    out = dict(s)
    out.update({
        "hist_acc": hist_acc, "hist_boxes": hist_boxes,
        "acc_per_box": rate, "acc_per_box_basis": rate_basis,
        "expected_boxes": boxes, "expected_boxes_basis": box_basis,
        "capacity": cap,
    })
    return out


# ── THE SPLIT — the goal over the selected stores, and it foots ───────────────────────────────────
def _largest_remainder(shares, total):
    """Round `shares` (a list of floats) to whole dollars so they SUM to `total` exactly. PURE.

    Independent rounding misses the goal by up to one dollar per store — on 28 stores that is a column
    that visibly does not add up to the number the owner typed. The residue goes to the largest
    fractional parts, so the adjustment lands where it is proportionally smallest.
    """
    if not shares:
        return []
    floors = [int(x) for x in shares]
    residue = int(round(_f(total))) - sum(floors)
    if residue == 0:
        return [float(x) for x in floors]
    if residue < 0:
        # Unreachable for well-formed input (truncating floors can only UNDER-shoot a total the shares
        # already sum to), so this is the defensive arm: take the overage off the smallest fractions,
        # one dollar each, never below zero, and bounded by the row count.
        order = sorted(range(len(shares)), key=lambda i: (shares[i] - floors[i]))
        for i in order:
            if residue >= 0:
                break
            if floors[i] > 0:
                floors[i] -= 1
                residue += 1
        return [float(x) for x in floors]
    order = sorted(range(len(shares)), key=lambda i: -(shares[i] - floors[i]))
    for k in range(residue):
        floors[order[k % len(order)]] += 1
    return [float(x) for x in floors]


def allocate(rows, goal, selected=None):
    """Split `goal` over the selected stores in proportion to capacity. PURE.

    `rows` are `capacity_row` outputs for EVERY store in the window. `selected` is a collection of
    store codes, or None/empty meaning every store. Returns
    `{assignments, reserved, to_split, weight_basis, unweighted, reason, shortfall}`.

    An UNSELECTED store is not re-planned and not assumed away: its current target is RESERVED out of
    the goal, and only what is left is split. That is what makes "assign to these three stores" an
    honest operation rather than a silent promise about the other twenty-five.
    """
    allrows = list(rows or ())
    sel = {str(c).strip().upper() for c in (selected or ()) if str(c or "").strip()}
    if not sel:
        sel = {str(r.get("store_code") or "").strip().upper() for r in allrows}
        sel.discard("")
    chosen = [r for r in allrows if str(r.get("store_code") or "").strip().upper() in sel]
    others = [r for r in allrows if str(r.get("store_code") or "").strip().upper() not in sel]

    reserved = _r2(sum(_f(r.get("current_target")) for r in others))
    base = {"assignments": {}, "reserved": reserved, "to_split": None,
            "weight_basis": None, "unweighted": [], "reason": None, "shortfall": None}
    if goal is None:
        return dict(base, reason="no_goal")
    if not chosen:
        return dict(base, reason="no_stores_selected")

    to_split = _r2(_f(goal) - reserved)
    if to_split <= 0:
        # Not clamped to zero and not forced: the targets already set on the stores NOT being assigned
        # account for the whole company goal, which is a planning fact the owner needs told.
        return dict(base, to_split=to_split, reason="goal_already_committed",
                    shortfall=_r2(reserved - _f(goal)))

    caps = [(_f(r.get("capacity")) if r.get("capacity") is not None else 0.0) for r in chosen]
    total_cap = sum(caps)
    if total_cap > 0:
        weight_basis = "capacity"
        # A store with no measurable capacity is dropped from the SPLIT entirely rather than given a
        # share of zero: a zero in the assignments is written to its target row, and writing $0 over a
        # target the report could not compute is the silent wipe §61's honesty rules forbid. It is
        # named in `unweighted` instead, so the page can say why it has no suggestion.
        unweighted = [str(r.get("store_code")) for r, c in zip(chosen, caps) if c <= 0]
        chosen = [r for r, c in zip(chosen, caps) if c > 0]
        caps = [c for c in caps if c > 0]
        total_cap = sum(caps)
        shares = [to_split * c / total_cap for c in caps]
    else:
        # Nothing in the window is measurable at all (no boxes anywhere). An equal split is the only
        # defensible answer, and the basis says it is not proportional to anything.
        weight_basis = "equal_no_measurable_capacity"
        shares = [to_split / len(chosen)] * len(chosen)
        unweighted = [str(r.get("store_code")) for r in chosen]

    rounded = _largest_remainder(shares, to_split)
    return {"assignments": {str(r.get("store_code")): amt for r, amt in zip(chosen, rounded)},
            "reserved": reserved, "to_split": to_split, "weight_basis": weight_basis,
            "unweighted": unweighted, "reason": None, "shortfall": None}


# ── THE REPORT — the owner's columns, in his order ────────────────────────────────────────────────
def plan(stores, mode=None, value=None, basis=None, selected=None):
    """The whole report: one row per store with every column the owner named, plus the totals. PURE.

    Column order is his sentence: last two months in their own columns, projected this month, the
    current target, the EXTENDED target needed to meet the company goal, and the proportionate target
    derived from accessories-per-box history against the boxes actually being sold.
    """
    src = list(stores or ())
    amounts = basis_amounts(src)
    g = company_goal(mode=mode, value=value, basis=basis, basis_amounts=amounts)
    house = company_rate(src)
    rows = [capacity_row(s, house_rate=house) for s in src]
    alloc = allocate(rows, g.get("goal"), selected=selected)

    sel = {str(c).strip().upper() for c in (selected or ()) if str(c or "").strip()}
    out = []
    for r in rows:
        code = str(r.get("store_code") or "")
        picked = (not sel) or code.strip().upper() in sel
        suggested = alloc["assignments"].get(code)
        cur = _r2(r.get("current_target"))
        row = dict(r)
        row.update({
            "selected": picked,
            "suggested_target": suggested,
            # "extended target to meet the company goal" — what this store is being asked for ON TOP
            # of what it is already carrying. Negative when the goal asks it for less.
            "extension": (None if suggested is None else _r2(suggested - cur)),
            # The attachment rate the suggestion implies, so a manager can see whether the ask is
            # "sell more boxes" or "attach more per box" before arguing about the dollars.
            "required_acc_per_box": (None if (suggested is None or not r.get("expected_boxes"))
                                     else round(_f(suggested) / _f(r["expected_boxes"]), 4)),
            # The suggestion against where the store is already heading with no change at all.
            "gap_vs_projection": (None if suggested is None
                                  else _r2(suggested - _f(r.get("projected_acc")))),
            "share_pct": (None if (suggested is None or not alloc["to_split"])
                          else round(_f(suggested) / _f(alloc["to_split"]) * 100.0, 2)),
        })
        out.append(row)
    out.sort(key=lambda r: (-_f(r.get("capacity")), str(r.get("address") or r.get("store_code") or "")))

    def _sum(key, picked_only=False):
        return _r2(sum(_f(r.get(key)) for r in out if (r.get("selected") or not picked_only)))

    assigned = _r2(sum(_f(v) for v in alloc["assignments"].values()))
    return {
        "goal": g,
        "goal_basis_amounts": amounts,
        "company_acc_per_box": house,
        "allocation": {k: v for k, v in alloc.items() if k != "assignments"},
        "rows": out,
        "totals": {
            "m2_acc": _sum("m2_acc"), "m1_acc": _sum("m1_acc"),
            "mtd_acc": _sum("mtd_acc"), "projected_acc": _sum("projected_acc"),
            "current_target": _sum("current_target"),
            "assigned": assigned,
            "assigned_plus_reserved": _r2(assigned + _f(alloc["reserved"])),
            "extension": _r2(sum(_f(r.get("extension")) for r in out
                                 if r.get("extension") is not None)),
            "stores": len(out),
            "stores_selected": sum(1 for r in out if r.get("selected")),
        },
    }


# ── THE WRITE — what the caller is about to put in mig 006's row, checked before it goes ──────────
def assignment_payload(plan_result, store_codes=None):
    """`[{store_code, accessories_monthly}]` for the stores that actually have a suggestion. PURE.

    The caller writes these through §5's own gated `PUT /targets/{period}` path. A store with no
    suggestion is OMITTED rather than written as 0 — a planning page must not be able to wipe a
    target it could not compute.
    """
    want = {str(c).strip().upper() for c in (store_codes or ()) if str(c or "").strip()}
    out = []
    for r in (plan_result or {}).get("rows") or ():
        code = str(r.get("store_code") or "").strip()
        if not code or r.get("suggested_target") is None:
            continue
        if want and code.upper() not in want:
            continue
        out.append({"store_code": code, "accessories_monthly": _r2(r["suggested_target"])})
    return out
