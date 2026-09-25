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


def _s(v):
    return "" if v is None else str(v).strip()


def normalise_rules(rows, revenue_lines):
    """({field: {value_lower: pl_line_key}}, problems[]). A rule naming a line that is not a revenue line of the chart
    is REJECTED and reported (a sale is revenue; routing it to COGS/opex from here is not a decision this map takes)."""
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
        idx[f].setdefault(v, k)
    return idx, problems


def match(idx, department, category, product):
    """(pl_line_key, matched field, matched value) or None — product first, then category, then department."""
    vals = {"product": _s(product), "category": _s(category), "department": _s(department)}
    for f in MATCH_FIELDS:
        v = vals[f]
        if v and v.lower() in idx.get(f, {}):
            return idx[f][v.lower()], f, v
    return None


class Tally:
    """What a sales scan passed over, per (department, category): amount, rows, stores — and what it suppressed."""

    def __init__(self):
        self.unbooked, self.suppressed, self.mapped = {}, {}, {}

    @staticmethod
    def _add(bucket, key, amt, store):
        e = bucket.setdefault(key, {"amount": 0.0, "rows": 0, "stores": set()})
        e["amount"] = round(e["amount"] + float(amt or 0), 2)
        e["rows"] += 1
        if store:
            e["stores"].add(store)

    def unclaimed(self, department, category, amt, store):
        self._add(self.unbooked, (_s(department) or "(blank)", _s(category) or "(blank)"), amt, store)

    def suppressed_by_royalty(self, pl_line_key, department, category, amt, store):
        self._add(self.suppressed, (pl_line_key, _s(department) or "(blank)", _s(category) or "(blank)"), amt, store)

    def booked_by_map(self, pl_line_key, field, value, amt, store):
        self._add(self.mapped, (pl_line_key, field, value), amt, store)

    def side_line(self):
        """The line-shaped side entry coa attaches to its inputs (no by_store / company_wide dollars — nothing reads
        it as a P&L line; engine._assemble walks the spec, never this key)."""
        def rows(bucket, names):
            out = []
            for k, e in sorted(bucket.items(), key=lambda kv: -abs(kv[1]["amount"])):
                out.append({**dict(zip(names, k)), "amount": e["amount"], "rows": e["rows"], "stores": sorted(e["stores"])})
            return out
        unb = rows(self.unbooked, ("department", "category"))
        return {"by_store": {}, "company_wide": 0.0, "detail": {},
                "unbooked": unb, "unbooked_total": round(sum(u["amount"] for u in unb), 2),
                "suppressed": rows(self.suppressed, ("pl_line_key", "department", "category")),
                "mapped": rows(self.mapped, ("pl_line_key", "match_field", "match_value"))}
