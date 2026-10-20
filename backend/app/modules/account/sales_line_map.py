"""A SALE LINE NO CLASSIFIER CLAIMS — config-driven booking and a REPORTED remainder, for every tenant (owner
2026-09-25, mig 1022, index §37.4). PURE.

THE CLASS (named, not the instance). `coa.build_inputs` books a POS sale line only when one of its classifiers claims
it — an owner-picked service-fee product, the ePay fee matcher, the accessory config, the device department map. A
line none of them claims (a shipping charge, a copy job, a notary fee — any non-wireless sale, and any wireless sale
whose department nobody mapped) fell off the end of an if/elif chain and booked NOTHING, with nothing on the
statement saying so. That is true for every tenant, not only the one that surfaced it.

THE FIX (one home, dereferenced):
  · `commcalc.pl_sales_line_map` (per org; match on department | category | product → a REVENUE line of the chart,
    PL_SPEC) — the tenant's decision, config never code. Evaluated ONLY for a line every existing classifier passed
    over, so a line that books today books exactly as it does today: with no rows the P&L is byte-identical for every
    tenant (the house org included — proved in backend/harness_royalty.py §H on the real engine._assemble).
  · what still books nothing is TALLIED here and carried on the inputs' `_unbooked_sales` side entry (line-shaped,
    so every reader that walks the inputs stays safe), surfaced as statement meta `unbooked_sales` and on
    GET /account/pl-sales-map — never silent.
  · a line whose target the franchise royalty report already books for the period is SUPPRESSED and reported (the
    `add_comm` / covered-lines shape of §4b): the royalty report is the revenue of record for those lines, so a POS
    line mapped to the same line would count the same sale twice.
"""
from __future__ import annotations

MATCH_FIELDS = ("product", "category", "department")      # precedence: the most specific field wins
SIDE_KEY = "_unbooked_sales"

# ── THE BLANK LABEL HAS ONE SPELLING (owner ruling 2026-10-20, index §37.11) ─────────────────────
# A POS feed rings a plan activation with NO department at all. `Tally` has always PRINTED that
# department as "(blank)" on the unbooked report, but `normalise_rules` REJECTED a rule whose
# match_value was empty, so the one department the owner could see on the report was the one
# department he could not map. The token is now stated ONCE and read by the matcher AND the tally,
# so "what the report calls it" and "what a rule may name" cannot drift apart.
BLANK_VALUE = "(blank)"

# ── WHAT A MAPPED SALE LINE BOOKS IS A BASIS, NOT ALWAYS THE GROSS (owner ruling 2026-10-20) ─────
# Owner, verbatim: "commission and anythig like margin which comes in considered as income other
# than the money reimbursed towards the phone cost as subsidy".
#
# THE CLASS, not the instance. This map could only ever book `ext_price` — the GROSS the customer
# paid. For a whole family of POS lines that is the wrong number and there was no way to say so:
#   · a wallet / airtime refill rings the customer's money as `ext_price` and pays the store its
#     MARGIN in `gp`. Booking the gross would book pass-through money as income;
#   · a plan activation rings `ext_price` ~0 (the customer pays the carrier) and the store's whole
#     earning is `gp`. There was nothing to book at all.
# So a rule now DECLARES its basis, per rule, as config:
#   'ext' (house default) — Σ ext_price: a sale, or a fee the store charges, booked at full price;
#   'gp'  — Σ gp: the store's MARGIN on money that is otherwise a pass-through.
# `ext` is the default, so a pre-1088 schema and every existing rule book exactly what they book
# today. RULE TWO: no department, product, carrier or tenant name appears here — the basis is a
# column on the org's own rule row.
AMOUNT_BASES = ("ext", "gp")
DEFAULT_BASIS = "ext"


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def resolve_basis(v):
    """THE basis vocabulary's one resolver. Anything unrecognised (None, '', a pre-1088 row with no
    such column, a typo) reads the house default — a rule can never book nothing by accident."""
    b = _s(v).lower()
    return b if b in AMOUNT_BASES else DEFAULT_BASIS


def rule_amount(basis, ext, gp):
    """THE one home for "what does a mapped sale line book?". Every caller dereferences this; none
    may pick between ext_price and gp for itself (locked). PURE."""
    return _f(gp) if resolve_basis(basis) == "gp" else _f(ext)


def line_money(ext, gp):
    """THE one home for "does this sale line carry money?" — and money is the GROSS **or** the
    MARGIN (owner ruling 2026-10-20, index §37.11).

    THE CLASS this repairs. The sales scan's remainder branch was gated on `ext_price` alone, so a
    line whose only money is its `gp` was neither booked NOR tallied: it fell past the end of the
    if/elif chain exactly as §37.4's whole class did, and the hole-reporting built to catch that
    class could not see it either — the silent zero INSIDE the silent-zero report. It is not one
    tenant's problem: on BOTH orgs carrying a POS feed, the department with no label at all is the
    one holding the most margin against ~$0.00 of gross (the figures are in index §37.11, measured
    live, so this module names no tenant). PURE."""
    return bool(_f(ext)) or bool(_f(gp))


def match_value_of(v):
    """The value a rule may name for this field's cell — the blank label spelled once (above)."""
    return _s(v) or BLANK_VALUE


def normalise_rules(rows, revenue_lines):
    """({field: {value_lower: (pl_line_key, basis)}}, problems[]). A rule naming a line that is not a
    revenue line of the chart is REJECTED and reported (a sale is revenue; routing it to COGS/opex from
    here is not a decision this map takes). A rule carrying no basis, or a basis outside the vocabulary,
    reads the house default through the one resolver — never a second default spelled here."""
    idx = {f: {} for f in MATCH_FIELDS}
    problems = []
    for r in rows or []:
        if r.get("is_active") is False:
            continue
        f = _s(r.get("match_field")).lower()
        v = _s(r.get("match_value")).lower()
        k = _s(r.get("pl_line_key"))
        if f not in MATCH_FIELDS or not v:
            problems.append(f"rule {f or '?'}='{v}' is incomplete")
            continue
        if k not in revenue_lines:
            problems.append(f"rule {f}='{v}' names '{k}', which is not a revenue line of the chart")
            continue
        idx[f].setdefault(v, (k, resolve_basis(r.get("amount_basis"))))
    return idx, problems


def match(idx, department, category, product):
    """(pl_line_key, matched field, matched value, basis) or None — product first, then category, then
    department. A blank cell is matched under the ONE blank label, so the department the unbooked report
    prints as "(blank)" is the department a rule can name."""
    vals = {"product": match_value_of(product), "category": match_value_of(category),
            "department": match_value_of(department)}
    for f in MATCH_FIELDS:
        v = vals[f]
        if v and v.lower() in idx.get(f, {}):
            k, basis = idx[f][v.lower()]
            return k, f, v, basis
    return None


class Tally:
    """What a sales scan passed over, per (department, category): GROSS, MARGIN, rows, stores — and what
    it suppressed.

    BOTH AMOUNTS ARE CARRIED (owner ruling 2026-10-20, index §37.11). The tally used to record one
    figure, the line's `ext_price`, which made a margin-only department read as a rounding error on the
    very report built to make unbooked money impossible to miss. Every bucket now carries `amount` (the
    gross, unchanged — every existing reader keeps its number) AND `gp` beside it, with
    `unbooked_gp_total` on the side entry."""

    def __init__(self):
        self.unbooked, self.suppressed, self.mapped = {}, {}, {}

    @staticmethod
    def _add(bucket, key, amt, store, gp=0.0):
        e = bucket.setdefault(key, {"amount": 0.0, "gp": 0.0, "rows": 0, "stores": set()})
        e["amount"] = round(e["amount"] + _f(amt), 2)
        e["gp"] = round(e["gp"] + _f(gp), 2)
        e["rows"] += 1
        if store:
            e["stores"].add(store)

    def unclaimed(self, department, category, amt, store, gp=0.0):
        self._add(self.unbooked, (match_value_of(department), match_value_of(category)), amt, store, gp)

    def suppressed_by_royalty(self, pl_line_key, department, category, amt, store, gp=0.0):
        self._add(self.suppressed, (pl_line_key, match_value_of(department), match_value_of(category)),
                  amt, store, gp)

    def booked_by_map(self, pl_line_key, field, value, amt, store, gp=0.0):
        self._add(self.mapped, (pl_line_key, field, value), amt, store, gp)

    def side_line(self):
        """The line-shaped side entry coa attaches to its inputs (no by_store / company_wide dollars —
        nothing reads it as a P&L line; engine._assemble walks the spec, never this key)."""
        def rows(bucket, names):
            out = []
            for k, e in sorted(bucket.items(), key=lambda kv: -max(abs(kv[1]["amount"]), abs(kv[1]["gp"]))):
                out.append({**dict(zip(names, k)), "amount": e["amount"], "gp": e["gp"],
                            "rows": e["rows"], "stores": sorted(e["stores"])})
            return out
        unb = rows(self.unbooked, ("department", "category"))
        return {"by_store": {}, "company_wide": 0.0, "detail": {},
                "unbooked": unb, "unbooked_total": round(sum(u["amount"] for u in unb), 2),
                "unbooked_gp_total": round(sum(u["gp"] for u in unb), 2),
                "suppressed": rows(self.suppressed, ("pl_line_key", "department", "category")),
                "mapped": rows(self.mapped, ("pl_line_key", "match_field", "match_value"))}
